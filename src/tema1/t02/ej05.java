package tema1.t02;

import java.util.Scanner;

public class ej05 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Cual es su direccion:");
        String direccion = sc.nextLine();
        System.out.println("Cual es su numero telefonico:");
        int telefono = sc.nextInt();

        System.out.println("Su direccion colocada es: " + direccion);
        System.out.println("Su numero telefonico es: " + telefono);
    }
}
