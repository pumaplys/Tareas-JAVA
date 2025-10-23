package tema2.t03;

import java.util.Scanner;

public class ej03 {
    public static void main(String[]args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Cual es tu nombre");
        String nombre=sc.nextLine();
        System.out.println("Cual es tu edad");
        int edad=sc.nextInt();
        sc.close();

        MensajeNE(nombre,edad);
    }

    private static void MensajeNE(String nombre, int edad) {
        System.out.println("Tu nombre es " + nombre + " Y tu edad es "+ edad);
    }
}
