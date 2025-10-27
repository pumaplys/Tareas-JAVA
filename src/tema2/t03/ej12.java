package tema2.t03;

import java.util.Scanner;

public class ej12 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Cual es tu nombre");
        String nombre = sc.nextLine();
        System.out.println("Cual es tu edad");
        int edad = sc.nextInt();
        System.out.println("Cual es tu peso");
        double peso = sc.nextDouble();
        sc.close();

        MiPersona(nombre, edad, peso);
    }

    static void MiPersona(String nombre, int edad, double peso) {
        System.out.println("Tu nombre es: " + nombre + ". Tu edad es: " + edad + " y tu peso es de: " + peso + "kg");
    }
}
