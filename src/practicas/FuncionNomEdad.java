package practicas;

import java.util.Scanner;

public class FuncionNomEdad {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("cual es tu nombre");
        String nombre = sc.nextLine();
        System.out.println("cual es tu edad");
        int edad = sc.nextInt();

        sc.close();

        mensajeAlUsuario(nombre, edad);
    }

    static void mensajeAlUsuario(String nombre, int edad) {
        System.out.println("Hola " + nombre + " tienes " + edad + " anios");
    }

}
